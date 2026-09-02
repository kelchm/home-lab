import time

import pytest
from app.jobs import JobManager
from app.main import create_app
from fastapi.testclient import TestClient


def _wait_for(client, auth, job_id, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.get(f"/admin/v1/jobs/{job_id}", headers=auth).json()
        if job["status"] != "running":
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_switch_runs_and_journals(settings, auth, monkeypatch):
    monkeypatch.setattr("app.jobs.default_command", lambda kind, profile: ["sh", "-c", f"echo ran {kind} {profile}"])
    with TestClient(create_app(settings)) as client:
        accepted = client.post("/admin/v1/switches", json={"profile": "glm"}, headers=auth)
        assert accepted.status_code == 202
        job = _wait_for(client, auth, accepted.json()["id"])
        assert job["status"] == "succeeded"
        assert "ran switch glm" in job["log"]
        assert client.get("/admin/v1/jobs", headers=auth).json()[0]["id"] == job["id"]


def test_second_job_conflicts_while_running(settings, auth, monkeypatch):
    monkeypatch.setattr("app.jobs.default_command", lambda kind, profile: ["sleep", "2"])
    with TestClient(create_app(settings)) as client:
        first = client.post("/admin/v1/down", headers=auth)
        assert first.status_code == 202
        second = client.post("/admin/v1/switches", json={"profile": "glm"}, headers=auth)
        assert second.status_code == 409
        assert second.json()["detail"]["job"]["id"] == first.json()["id"]


def test_failed_command_is_reported(settings, auth, monkeypatch):
    monkeypatch.setattr("app.jobs.default_command", lambda kind, profile: ["sh", "-c", "echo boom; exit 3"])
    with TestClient(create_app(settings)) as client:
        accepted = client.post("/admin/v1/down", headers=auth)
        job = _wait_for(client, auth, accepted.json()["id"])
        assert job["status"] == "failed"
        assert job["rc"] == 3
        assert "boom" in job["log"]


def test_rapid_jobs_get_distinct_journals(settings, auth, monkeypatch):
    monkeypatch.setattr("app.jobs.default_command", lambda kind, profile: ["true"])
    with TestClient(create_app(settings)) as client:
        ids = []
        for _ in range(2):
            accepted = client.post("/admin/v1/down", headers=auth)
            assert accepted.status_code == 202
            job = _wait_for(client, auth, accepted.json()["id"])
            ids.append(job["id"])
        assert len(set(ids)) == 2
        assert len(client.get("/admin/v1/jobs", headers=auth).json()) == 2


def test_corrupt_journal_entry_does_not_break_listing(settings, auth):
    corrupt_dir = settings.jobs_dir / "20200101T000000Z-down-abc123"
    corrupt_dir.mkdir(parents=True)
    (corrupt_dir / "meta.json").write_text("{not json")
    with TestClient(create_app(settings)) as client:
        listing = client.get("/admin/v1/jobs", headers=auth)
        assert listing.status_code == 200
        assert listing.json()[0]["status"] == "corrupt-journal"


def test_job_id_path_traversal_is_rejected(settings, auth):
    with TestClient(create_app(settings)) as client:
        response = client.get("/admin/v1/jobs/..%2f..%2fetc", headers=auth)
        assert response.status_code == 404


def test_token_rotation_takes_effect_without_restart(settings, auth):
    with TestClient(create_app(settings)) as client:
        assert client.get("/admin/v1/state", headers=auth).status_code == 200
        settings.token_file.write_text("rotated-token\n")
        assert client.get("/admin/v1/state", headers=auth).status_code == 401
        rotated = {"Authorization": "Bearer rotated-token"}
        assert client.get("/admin/v1/state", headers=rotated).status_code == 200


def test_setup_failure_releases_lock(settings, auth, monkeypatch):
    original_write_meta = JobManager._write_meta
    failures = iter([OSError("disk full")])

    def flaky_write_meta(self, job):
        exc = next(failures, None)
        if exc is not None:
            raise exc
        return original_write_meta(self, job)

    monkeypatch.setattr(JobManager, "_write_meta", flaky_write_meta)
    monkeypatch.setattr("app.jobs.default_command", lambda kind, profile: ["true"])
    with TestClient(create_app(settings)) as client:
        with pytest.raises(OSError):
            client.post("/admin/v1/down", headers=auth)
        accepted = client.post("/admin/v1/down", headers=auth)
        assert accepted.status_code == 202
        assert _wait_for(client, auth, accepted.json()["id"])["status"] == "succeeded"
