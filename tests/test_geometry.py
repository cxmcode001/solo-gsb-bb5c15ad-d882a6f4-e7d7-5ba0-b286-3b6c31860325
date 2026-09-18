"""Geometry tests: clock wrapping, radial overlap, arc merging."""
from app.geometry import (
    arcs_intersect,
    make_arc,
    merge_clock_arcs,
    radial_overlap,
)


def test_wrapping_arc_intersects_past_twelve():
    wrap = make_arc(11, 1)          # 330deg -> 60deg, 60deg span
    assert arcs_intersect(wrap, make_arc(11.5, 11.8))
    assert arcs_intersect(wrap, make_arc(0, 0))
    assert arcs_intersect(wrap, make_arc(0.5, 0.8))
    assert not arcs_intersect(wrap, make_arc(2, 3))
    assert not arcs_intersect(wrap, make_arc(9, 10))
    # Reverse long way: 1 -> 11 must NOT meet 12 o'clock.
    other = make_arc(1, 11)
    assert arcs_intersect(other, make_arc(6, 6))
    assert not arcs_intersect(other, make_arc(0, 0))


def test_point_arcs():
    assert arcs_intersect(make_arc(3, 3), make_arc(2, 4))
    assert arcs_intersect(make_arc(3, 3), make_arc(3, 3))
    assert not arcs_intersect(make_arc(3, 3), make_arc(4, 5))
    # 0 and 12 are the same physical point.
    assert arcs_intersect(make_arc(0, 0), make_arc(12, 12))


def test_radial_overlap():
    assert radial_overlap(0, 2, 1.5, 3)
    assert radial_overlap(0, 1, 1, 2)        # touching
    assert not radial_overlap(0, 1, 1.01, 2)


def test_merge_simple():
    assert merge_clock_arcs([(1, 2), (1.5, 3)]) == [(1.0, 3.0, False)]


def test_merge_wrapping_output():
    arcs = merge_clock_arcs([(11, 12), (0, 1)])
    assert arcs == [(11.0, 13.0, True)]


def test_merge_isolated_wrapping_arc_preserved():
    arcs = merge_clock_arcs([(11, 1), (5, 6)])
    assert arcs == [(5.0, 6.0, False), (11.0, 13.0, True)]


def test_merge_end_at_twelve():
    assert merge_clock_arcs([(10, 12)]) == [(10.0, 12.0, False)]
    assert merge_clock_arcs([(12, 12)]) == [(0.0, 0.0, False)]


def test_merge_full_ring():
    arcs = merge_clock_arcs([(11, 5), (4, 12)])
    assert arcs == [(0.0, 12.0, False)]


def test_merge_wrapping_contains_inner_arc():
    # 11.5 -> 0.5 covers 0.2 -> 0.4 entirely.
    assert merge_clock_arcs([(11.5, 0.5), (0.2, 0.4)]) == [
        (11.5, 12.5, True)
    ]


def test_merge_two_wrapping_arcs():
    # 10 -> 1 and 11 -> 2 together cover 10 -> 2.
    assert merge_clock_arcs([(10, 1), (11, 2)]) == [(10.0, 14.0, True)]


def test_merge_wrapping_head_touch_only():
    # 11 -> 12 touches 0 -> 1 only via the 12 o'clock seam.
    assert merge_clock_arcs([(11, 12), (0, 1)]) == [(11.0, 13.0, True)]


def test_merge_point_on_seam():
    assert merge_clock_arcs([(11, 1), (12, 12)]) == [(11.0, 13.0, True)]
    assert merge_clock_arcs([(3, 4), (12, 12)]) == [
        (0.0, 0.0, False),
        (3.0, 4.0, False),
    ]
