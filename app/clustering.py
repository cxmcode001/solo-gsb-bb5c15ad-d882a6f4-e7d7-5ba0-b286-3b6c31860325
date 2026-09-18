"""连通簇聚类与裂纹轨迹合并。

规则：两条观察的 *径向区间* 与 *顺时针钟位弧* 同时相交（端点相接算相交）
则并入同一连通簇；连通关系按并查集传递闭包。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Dict, List, Sequence

from . import geometry as geo


@dataclass
class Observation:
    observation_id: str
    inspection_id: str
    inspected_at: str
    batch_id: str
    turbine_id: str
    blade_id: str
    radius_start: float
    radius_end: float
    clock_start: float
    clock_end: float
    grade: int
    confidence: float

    @property
    def arc(self) -> geo.Arc:
        return to_arc_cached(self)


def to_arc_cached(obs: "Observation") -> geo.Arc:
    arc = getattr(obs, "_arc", None)
    if arc is None:
        arc = geo.to_arc(obs.clock_start, obs.clock_end)
        object.__setattr__(obs, "_arc", arc)
    return arc


class DSU:
    def __init__(self, n: int):
        self.parent = list(range(n))
        self.rank = [0] * n

    def find(self, x: int) -> int:
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[x] != x:
            self.parent[x], x = root, self.parent[x]
        return root

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        if self.rank[ra] < self.rank[rb]:
            ra, rb = rb, ra
        self.parent[rb] = ra
        if self.rank[ra] == self.rank[rb]:
            self.rank[ra] += 1


def _compatible(a: Observation, b: Observation) -> bool:
    if not geo.radial_intersects(a.radius_start, a.radius_end,
                                 b.radius_start, b.radius_end):
        return False
    return geo.arcs_intersect(to_arc_cached(a), to_arc_cached(b))


def cluster_observations(observations: Sequence[Observation]) -> List[List[Observation]]:
    """按“径向相交 ∧ 钟位弧相交”求连通簇。

    观察数量为叶片级（通常数十至数百条），O(n²) 两两判定足够。
    """
    n = len(observations)
    dsu = DSU(n)
    for i in range(n):
        oi = observations[i]
        for j in range(i + 1, n):
            oj = observations[j]
            if _compatible(oi, oj):
                dsu.union(i, j)

    groups: Dict[int, List[Observation]] = {}
    for i, obs in enumerate(observations):
        groups.setdefault(dsu.find(i), []).append(obs)

    clusters = list(groups.values())
    for cluster in clusters:
        cluster.sort(key=lambda o: (o.radius_start, o.radius_end,
                                    o.clock_start, o.clock_end,
                                    o.observation_id))
    clusters.sort(key=lambda c: (c[0].radius_start, c[0].observation_id))
    return clusters


def _signature(turbine_id: str, blade_id: str,
               members: Sequence[Observation]) -> str:
    """由簇成员稳定生成轨迹标识：同成员集合重算后 ID 不变。"""
    digest_src = turbine_id + "|" + blade_id + "|" + ",".join(
        sorted(m.observation_id for m in members)
    )
    return "trj_" + hashlib.sha1(digest_src.encode("utf-8")).hexdigest()[:16]


def build_trajectory(turbine_id: str, blade_id: str,
                     members: Sequence[Observation]) -> Dict[str, Any]:
    """把一个连通簇合并为一条裂纹轨迹。"""
    radius_start = min(m.radius_start for m in members)
    radius_end = max(m.radius_end for m in members)

    merged_segments, clock_span = geo.merge_arcs([to_arc_cached(m) for m in members])
    clock = geo.describe_clock(merged_segments)
    clock["span"] = round(clock_span, 4)

    # 等级 -> 该等级下的最高置信度
    grade_conf: Dict[int, float] = {}
    for m in members:
        grade_conf[m.grade] = max(grade_conf.get(m.grade, -1.0), m.confidence)
    grades = sorted(grade_conf)
    has_conflict = len(grades) > 1

    # 代表等级：最高置信度优先；并列时取等级数值更大（更保守）
    representative = max(grades, key=lambda g: (grade_conf[g], g))

    sources = [
        {
            "observation_id": m.observation_id,
            "inspection_id": m.inspection_id,
            "inspected_at": m.inspected_at,
            "batch_id": m.batch_id,
            "radius_start": round(m.radius_start, 4),
            "radius_end": round(m.radius_end, 4),
            "clock_start": round(m.clock_start, 4),
            "clock_end": round(m.clock_end, 4),
            "grade": m.grade,
            "confidence": round(m.confidence, 4),
        }
        for m in members
    ]
    sources.sort(key=lambda s: (s["radius_start"], s["clock_start"],
                                s["observation_id"]))

    return {
        "trajectory_id": _signature(turbine_id, blade_id, members),
        "turbine_id": turbine_id,
        "blade_id": blade_id,
        "radius_start": round(radius_start, 4),
        "radius_end": round(radius_end, 4),
        "clock": clock,
        "max_confidence": round(max(m.confidence for m in members), 4),
        "grades": grades,
        "grade_confidence": {str(g): round(c, 4) for g, c in grade_conf.items()},
        "representative_grade": representative,
        "grade_conflict": has_conflict,
        "observation_count": len(members),
        "sources": sources,
    }


def build_all(turbine_id: str, blade_id: str,
              observations: Sequence[Observation]) -> List[Dict[str, Any]]:
    return [build_trajectory(turbine_id, blade_id, cluster)
            for cluster in cluster_observations(observations)]
