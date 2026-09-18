"""Clustering / trajectory-merge tests."""
from app.clustering import cluster_observations


def _obs(
    obs_id,
    key,
    r_start,
    r_end,
    c_start,
    c_end,
    grade,
    confidence,
    turbine="T-01",
    blade="B1",
    batch="b1",
):
    return {
        "id": obs_id,
        "batch_id": batch,
        "obs_key": key,
        "turbine": turbine,
        "blade": blade,
        "r_start": r_start,
        "r_end": r_end,
        "clock_start": c_start,
        "clock_end": c_end,
        "grade": grade,
        "confidence": confidence,
        "observed_at": None,
        "source_code": f"#{obs_id:04d}",
    }


def test_connecting_chain_over_wrapping_clock():
    # a touches b over 12 o'clock, b touches c radially; a and c never
    # overlap directly but must end in one component.
    rows = [
        _obs(1, "a", 0.0, 1.0, 11.5, 12, grade=1, confidence=0.9),
        _obs(2, "b", 0.5, 2.0, 0, 0.5, grade=1, confidence=0.8),
        _obs(3, "c", 1.5, 3.0, 0.2, 0.4, grade=1, confidence=0.7),
    ]
    traj = cluster_observations(rows)
    assert len(traj) == 1
    t = traj[0]
    assert (t["r_start"], t["r_end"]) == (0.0, 3.0)
    assert t["clock_arcs"] == [{"start": 11.5, "end": 12.5, "wraps": True}]
    assert t["grade_conflict"] is False
    assert len(t["sources"]) == 3


def test_disjoint_by_clock():
    rows = [
        _obs(1, "a", 0, 5, 1, 2, grade=2, confidence=0.5),
        _obs(2, "b", 0, 5, 3, 4, grade=2, confidence=0.5),
    ]
    assert len(cluster_observations(rows)) == 2


def test_disjoint_by_radius():
    rows = [
        _obs(1, "a", 0, 1, 6, 6, grade=2, confidence=0.5),
        _obs(2, "b", 2, 3, 6, 6, grade=2, confidence=0.5),
    ]
    assert len(cluster_observations(rows)) == 2


def test_grade_conflict_and_dominant_grade():
    rows = [
        _obs(1, "a", 0, 2, 6, 7, grade=1, confidence=0.3),
        _obs(2, "b", 1, 3, 6, 7, grade=3, confidence=0.9),
        _obs(3, "c", 1, 3, 6, 7, grade=3, confidence=0.1),
    ]
    traj = cluster_observations(rows)
    assert len(traj) == 1
    t = traj[0]
    assert t["grade_conflict"] is True
    assert t["grade"] == 3  # highest summed confidence
    conf = {item["grade"]: item["confidence"] for item in t["grade_confidence"]}
    assert abs(conf[1] - 0.3) < 1e-9
    assert abs(conf[3] - 1.0) < 1e-9


def test_cluster_key_stable_for_same_members():
    rows_a = [
        _obs(1, "a", 0, 1, 6, 7, grade=1, confidence=0.5),
        _obs(2, "b", 1, 2, 6, 7, grade=2, confidence=0.5),
    ]
    rows_b = [
        _obs(99, "b", 1, 2, 6, 7, grade=2, confidence=0.5),
        _obs(42, "a", 0, 1, 6, 7, grade=1, confidence=0.5),
    ]
    k1 = cluster_observations(rows_a)[0]["cluster_key"]
    k2 = cluster_observations(rows_b)[0]["cluster_key"]
    assert k1 == k2
