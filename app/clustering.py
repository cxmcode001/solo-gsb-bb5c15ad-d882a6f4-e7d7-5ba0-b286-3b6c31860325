"""Connected-component clustering of inspection observations.

Two observations belong to the same crack trajectory when their radial
intervals overlap **and** their clock-position arcs intersect (12 o'clock
wrapping included). This pairwise relation is turned into connected
components with a union-find; components are then merged into a single crack
trajectory per blade.
"""
from __future__ import annotations

import hashlib
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Mapping

from .config import EPS
from .geometry import (
    FULL_CIRCLE,
    arcs_intersect,
    hour_to_deg,
    make_arc,
    merge_clock_arcs,
    radial_overlap,
)

# Keys carried through from the observations table.
_ROW_FIELDS = (
    "id",
    "batch_id",
    "obs_key",
    "turbine",
    "blade",
    "r_start",
    "r_end",
    "clock_start",
    "clock_end",
    "grade",
    "confidence",
    "observed_at",
    "source_code",
)


def _arc_wraps(c_start: float, c_end: float) -> bool:
    """True iff the clockwise interval from c_start to c_end crosses 12."""
    if abs(float(c_end) - float(c_start)) <= EPS:
        return False
    return hour_to_deg(c_end) < hour_to_deg(c_start) - EPS


class _UnionFind:
    def __init__(self, size: int) -> None:
        self.parent = list(range(size))

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            # Attach the larger root id under the smaller so component
            # identity is deterministic for a fixed member set.
            if ra < rb:
                self.parent[rb] = ra
            else:
                self.parent[ra] = rb


def _as_row(row: Any) -> Dict[str, Any]:
    if isinstance(row, Mapping):
        return dict(row)
    return dict(zip(_ROW_FIELDS, row))


def cluster_observations(rows: Iterable[Any]) -> List[Dict[str, Any]]:
    """Group one blade's observations into crack trajectories.

    Returns one dict per connected component, ordered from the blade root
    outward (smallest r_start first).
    """
    obs = [_as_row(row) for row in rows]
    if not obs:
        return []

    uf = _UnionFind(len(obs))
    arcs = [make_arc(o["clock_start"], o["clock_end"]) for o in obs]

    for i in range(len(obs)):
        oi = obs[i]
        for j in range(i + 1, len(obs)):
            oj = obs[j]
            if radial_overlap(
                oi["r_start"], oi["r_end"], oj["r_start"], oj["r_end"]
            ) and arcs_intersect(arcs[i], arcs[j]):
                uf.union(i, j)

    groups: Dict[int, List[int]] = defaultdict(list)
    for idx in range(len(obs)):
        groups[uf.find(idx)].append(idx)

    trajectories: List[Dict[str, Any]] = []
    for members in groups.values():
        members.sort(key=lambda k: obs[k]["id"])
        member_rows = [obs[k] for k in members]

        r_start = min(o["r_start"] for o in member_rows)
        r_end = max(o["r_end"] for o in member_rows)
        clock_arcs = merge_clock_arcs(
            [(o["clock_start"], o["clock_end"]) for o in member_rows]
        )

        # Confidence accumulated per grade: the grade with the highest total
        # confidence is the trajectory's adopted grade.
        grade_conf: Dict[int, float] = defaultdict(float)
        for o in member_rows:
            grade_conf[int(o["grade"])] += float(o["confidence"])
        dominant_grade = min(
            grade_conf,
            key=lambda g: (-round(grade_conf[g], 9), g),
        )
        grades_present = sorted(grade_conf)
        grade_conflict = len(grades_present) > 1

        identity = "|".join(
            f"{o['batch_id']}:{o['obs_key']}" for o in member_rows
        )
        cluster_key = hashlib.sha1(identity.encode("utf-8")).hexdigest()[:16]

        trajectories.append(
            {
                "turbine": member_rows[0]["turbine"],
                "blade": member_rows[0]["blade"],
                "cluster_key": cluster_key,
                "r_start": r_start,
                "r_end": r_end,
                "clock_arcs": [
                    {"start": s, "end": e, "wraps": w}
                    for s, e, w in clock_arcs
                ],
                "grade": dominant_grade,
                "grade_conflict": grade_conflict,
                "grade_confidence": [
                    {"grade": g, "confidence": round(grade_conf[g], 9)}
                    for g in grades_present
                ],
                "sources": [
                    {
                        "observation_id": o["id"],
                        "batch_id": o["batch_id"],
                        "obs_key": o["obs_key"],
                        "r_start": o["r_start"],
                        "r_end": o["r_end"],
                        "clock_start": o["clock_start"],
                        "clock_end": o["clock_end"],
                        "wraps": _arc_wraps(
                            o["clock_start"], o["clock_end"]
                        ),
                        "grade": o["grade"],
                        "confidence": o["confidence"],
                        "observed_at": o["observed_at"],
                        "source_code": o["source_code"],
                    }
                    for o in member_rows
                ],
            }
        )

    trajectories.sort(key=lambda t: (t["r_start"], t["cluster_key"]))
    return trajectories
