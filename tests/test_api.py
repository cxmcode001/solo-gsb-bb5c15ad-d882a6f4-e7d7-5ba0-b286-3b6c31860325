"""End-to-end API tests (in-process FastAPI + temp SQLite file)."""
from __future__ import annotations

import importlib
import os

import pytest


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DB_PATH", str(db_path))
    import app.config as config
    importlib.reload(config)
    import app.db as db
    import app.clustering as clustering
    importlib.reload(db)
    import app.schemas as schemas
    importlib.reload(schemas)
    import app.main as main
    importlib.reload(main)

    from fastapi.testclient import TestClient

    with TestClient(main.app) as c:
        yield c


def _payload(**overrides):
    obs = {
        "obs_key": "o1",
        "turbine": "T-01",
        "blade": "B1",
        "r_start": 0.0,
        "r_end": 2.0,
        "clock_start": 11.5,
        "clock_end": 0.5,
        "grade": 2,
        "confidence": 0.8,
    }
    obs.update(overrides)
    return {"observations": [obs]}


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_write_query_and_conflict(client):
    payload = {
        "observations": [
            {
                "obs_key": "a",
                "turbine": "T-01",
                "blade": "B1",
                "r_start": 0.0,
                "r_end": 2.0,
                "clock_start": 11.5,
                "clock_end": 0.5,
                "grade": 2,
                "confidence": 0.8,
            },
            {
                "obs_key": "b",
                "turbine": "T-01",
                "blade": "B1",
                "r_start": 1.5,
                "r_end": 3.0,
                "clock_start": 0.2,
                "clock_end": 0.4,
                "grade": 4,
                "confidence": 0.95,
            },
            # different blade: untouched
            {
                "obs_key": "c",
                "turbine": "T-01",
                "blade": "B2",
                "r_start": 5.0,
                "r_end": 6.0,
                "clock_start": 6,
                "clock_end": 7,
                "grade": 1,
                "confidence": 0.4,
            },
        ]
    }
    r = client.put("/api/batches/run-2026-01/observations", json=payload)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["action"] == "created"
    assert body["observations_written"] == 3
    assert body["blades_recomputed"] == 2

    cracks = client.get("/api/cracks", params={"turbine": "T-01"}).json()
    assert len(cracks) == 2
    b1 = next(c for c in cracks if c["blade"] == "B1")
    assert b1["grade_conflict"] is True
    assert b1["grade"] == 4
    assert b1["clock_arcs"] == [
        {"start": 11.5, "end": 12.5, "wraps": True}
    ]
    assert len(b1["sources"]) == 2
    assert {s["obs_key"] for s in b1["sources"]} == {"a", "b"}

    only_conflict = client.get(
        "/api/cracks", params={"only_conflicts": True}
    ).json()
    assert {c["blade"] for c in only_conflict} == {"B1"}


def test_batch_idempotent_replace(client):
    r1 = client.put("/api/batches/B/observations", json=_payload())
    assert r1.json()["action"] == "created"
    r2 = client.put("/api/batches/B/observations", json=_payload())
    assert r2.json()["action"] == "replaced"

    obs = client.get("/api/observations", params={"batch_id": "B"}).json()
    assert len(obs) == 1

    # replacing the batch with observations on a different blade must remove
    # the old blade's stale clusters.
    moved = _payload(blade="B9", obs_key="o1")
    client.put("/api/batches/B/observations", json=moved)
    cracks = client.get("/api/cracks").json()
    assert {c["blade"] for c in cracks} == {"B9"}


def test_batch_recompute_404_and_success(client):
    r = client.post("/api/batches/missing/recompute")
    assert r.status_code == 404

    client.put("/api/batches/B/observations", json=_payload())
    r = client.post("/api/batches/B/recompute")
    assert r.status_code == 200
    assert r.json()["blades_recomputed"] == 1

    r = client.post("/api/recompute")
    assert r.status_code == 200
    assert r.json()["scope"] == "all"


def test_validation_errors(client):
    bad = _payload(r_end=0.1, r_start=1.0)  # end < start
    r = client.put("/api/batches/B/observations", json=bad)
    assert r.status_code == 422

    bad = _payload(clock_start=13)
    r = client.put("/api/batches/B/observations", json=bad)
    assert r.status_code == 422

    bad = _payload(grade=9)
    r = client.put("/api/batches/B/observations", json=bad)
    assert r.status_code == 422

    dup = {"observations": [
        {
            "obs_key": "x",
            "turbine": "T",
            "blade": "B",
            "r_start": 0,
            "r_end": 1,
            "clock_start": 1,
            "clock_end": 2,
            "grade": 1,
            "confidence": 0.5,
        },
        {
            "obs_key": "x",
            "turbine": "T",
            "blade": "B",
            "r_start": 0,
            "r_end": 1,
            "clock_start": 1,
            "clock_end": 2,
            "grade": 1,
            "confidence": 0.5,
        },
    ]}
    r = client.put("/api/batches/B/observations", json=dup)
    assert r.status_code == 422
